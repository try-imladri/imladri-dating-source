import publicPlans from "../../backend/seed/public_plans.json" with { type: "json" };
import { catalogDocument, isCatalogDocument, isPublicPage } from "./public-catalog.js";
import { isPrivatePage } from "../../frontend/src/lib/publicSeo.mjs";
import { pageDocument } from "./seo.js";

const apiPath = path => path === "/api" || path.startsWith("/api/");
const probePath = "/api/ops/container-egress";
const repairPath = "/api/ops/repair-ingress";

const readinessCache = new WeakMap();
const repairAfter = new WeakMap();
const renderReadyUntil = new WeakMap();
const publicReadStandbyUntil = new WeakMap();
const transientGatewayStatuses = new Set([502, 503, 504]);
const publicReadDeadlineMs = 5000;

function discardBody(response) {
  // Once headers establish failure, a slow origin's cleanup must not delay routing.
  response?.body?.cancel().catch(() => {});
}

function canRetryPublicRead(request, path) {
  if (!["GET", "HEAD"].includes(request.method) || request.body) return false;
  return path === "/api/public/config" || path === "/api/public/catalog-paths"
    || /^\/api\/public\/(?:archetypes|figures)(?:\/[a-z0-9]+(?:-[a-z0-9]+)*)?$/.test(path);
}

async function primaryReady(service) {
  const cached = readinessCache.get(service);
  if (cached && cached.until > Date.now()) return cached.ok;
  // Workers can share a result across requests, but each readiness probe owns its I/O.
  const state = cached || { ok: false, until: 0, started: 0, completed: 0 };
  if (!cached) readinessCache.set(service, state);
  const sequence = ++state.started;
  const controller = new AbortController();
  let timer;
  let ok = false;
  try {
    const probe = async () => {
      const response = await service.fetch(new Request("https://imladri.com/api/ready", {
        signal: controller.signal, redirect: "manual"
      }));
      if (response.status !== 200) {
        discardBody(response);
        return false;
      }
      return (await response.json()).ready === true;
    };
    ok = await Promise.race([probe(), new Promise(resolve => {
      timer = setTimeout(() => { controller.abort(); resolve(false); }, 3500);
    })]);
  } catch { ok = false; } finally { clearTimeout(timer); }
  const newest = readinessCache.get(service);
  if (newest === state && sequence > state.completed) {
    state.completed = sequence;
    state.ok = ok;
    state.until = Date.now() + (ok ? 5_000 : 15_000);
  } else if (newest && newest.until > Date.now()) {
    return newest.ok;
  }
  return ok;
}

// A sleeping Render service may hold the *first* HTTP request for close to a minute.
// Wake it with a credential-free readiness read before forwarding a member action.
async function renderReady(route, env, fetchOrigin) {
  if (env.RENDER_COLD_START_GUARD !== "true" || !route.origin) return true;
  if ((renderReadyUntil.get(env) || 0) > Date.now()) return true;
  const controller = new AbortController();
  let timer;
  try {
    const probe = async () => {
      const headers = route.originSecret ? { "X-Imladri-Origin": route.originSecret } : {};
      const request = new Request(new URL("/api/ready", route.origin), {
        headers, signal: controller.signal, redirect: "manual"
      });
      const result = await fetchOrigin(request, { cache: "no-store" });
      if (result.status !== 200) {
        discardBody(result);
        return false;
      }
      return (await result.json()).ready === true;
    };
    const ok = await Promise.race([probe(), new Promise(resolve => {
      timer = setTimeout(() => { controller.abort(); resolve(false); }, 2500);
    })]);
    if (ok) renderReadyUntil.set(env, Date.now() + 30_000);
    return ok;
  } catch { return false; } finally { clearTimeout(timer); }
}

function originReconnecting() {
  return Response.json({ detail: "Imladri is reconnecting. Please try again in a few seconds." }, {
    status: 503, headers: { "Cache-Control": "private, no-store", "Retry-After": "3", "X-Imladri-Reconnecting": "1" }
  });
}

function requestRepair(env, ctx, fetchOrigin) {
  if (["postgresql", "render_gateway"].includes(env.IMLADRI_DATABASE_BACKEND)) return;
  if (!ctx || !env.OPS_CHECK_SECRET || (repairAfter.get(env.API) || 0) > Date.now()) return;
  repairAfter.set(env.API, Date.now() + 120_000);
  const req = new Request(new URL(repairPath, env.RENDER_FAILOVER_ORIGIN), {
    method: "POST", redirect: "manual", headers: {
      Authorization: `Bearer ${env.OPS_CHECK_SECRET}`, "X-Imladri-Origin": env.RENDER_ORIGIN_SECRET
    }
  });
  ctx.waitUntil(fetchOrigin(req, { cache: "no-store" })
    .then(response => response.body?.cancel()).catch(() => {}));
}

async function backend(env, ctx, fetchOrigin, maintainIngress = false, publicRead = false) {
  if (env.RENDER_FAILOVER_ORIGIN) {
    if (env.RENDER_ORIGIN_SECRET && env.RENDER_FAILOVER_ORIGIN === "https://imladri-personal-live-api.onrender.com") {
      const standby = { origin: env.RENDER_FAILOVER_ORIGIN, originSecret: env.RENDER_ORIGIN_SECRET };
      if (env.RENDER_FAILOVER_MODE === "auto" && env.API) {
        if (publicRead && publicReadStandbyUntil.get(env.API) > Date.now()) return standby;
        if (await primaryReady(env.API)) {
          if (maintainIngress) requestRepair(env, ctx, fetchOrigin);
          return { service: env.API, originSecret: env.RENDER_ORIGIN_SECRET, standby };
        }
        requestRepair(env, ctx, fetchOrigin);
      }
      return standby;
    }
    return {};
  }
  return { service: env.API, origin: env.API_ORIGIN, originSecret: env.RENDER_ORIGIN_SECRET };
}

export function publicResponse(upstream, publicURL, backendOrigin) {
  // Preserve separate Set-Cookie headers, including HttpOnly cookies and Expires dates.
  const result = new Response(upstream.body, upstream);
  result.headers.set("Cache-Control", "private, no-store");
  result.headers.delete("X-Imladri-Origin");
  result.headers.delete("X-Imladri-Client-IP");
  result.headers.delete("X-Imladri-Reconnecting");
  const location = result.headers.get("Location");
  if (location && backendOrigin) {
    const redirect = new URL(location, publicURL);
    if (redirect.origin === backendOrigin) {
      const publicLocation = new URL(publicURL);
      publicLocation.pathname = redirect.pathname;
      publicLocation.search = redirect.search;
      publicLocation.hash = redirect.hash;
      result.headers.set("Location", publicLocation.href);
    }
  }
  return result;
}

export async function handle(request, env, fetchOrigin = fetch, ctx) {
  const response = await routeRequest(request, env, fetchOrigin, ctx);
  // Apply the same browser protections to generated HTML, assets, API errors
  // and redirects. Keep stronger upstream policies and separate cookies intact.
  const secured = new Response(response.body, response);
  for (const [name, value] of Object.entries({
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
  })) if (!secured.headers.has(name)) secured.headers.set(name, value);
  if (new URL(request.url).protocol === "https:" && !secured.headers.has("Strict-Transport-Security")) {
    secured.headers.set("Strict-Transport-Security", "max-age=31536000");
  }
  return secured;
}

async function routeRequest(request, env, fetchOrigin, ctx) {
  const url = new URL(request.url);
  // Old invitation URLs contain a private fragment. Respond directly without opening the app.
  if (/^\/study\/volunteer\/?$/.test(url.pathname)
      || /^\/api\/studies\/(reader|first-visit|volunteer)(\/|$)/.test(url.pathname)
      || /^\/api\/admin\/research\/(field|volunteer-links|volunteers)(\/|$)/.test(url.pathname)) {
    return new Response(request.method === "HEAD" ? null : "This study is closed.", {
      status: 410,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "private, no-store, no-transform",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex"
      }
    });
  }
  const canonicalize = url.hostname === "www.imladri.com" || (url.hostname === "imladri.com" && url.protocol !== "https:");
  const oldClerkReturn = ["GET", "HEAD"].includes(request.method) && /^\/sso-callback\/?$/.test(url.pathname);
  if (canonicalize || oldClerkReturn) {
    if (canonicalize) {
      url.protocol = "https:";
      url.host = "imladri.com";
    }
    if (oldClerkReturn) url.pathname = "/app/start";
    return Response.redirect(url.href, oldClerkReturn ? 307 : 308);
  }
  if (["GET", "HEAD"].includes(request.method) && url.pathname === "/api/public/plans") {
    const headers = { "Content-Type": "application/json", "Cache-Control": "public, max-age=60" };
    return new Response(request.method === "HEAD" ? null : JSON.stringify({ plans: publicPlans }), { headers });
  }
  if (["GET", "HEAD"].includes(request.method) && isCatalogDocument(url.pathname)) {
    const read = path => handle(new Request(new URL(path, url), {headers: {Accept: "application/json"}}), env, fetchOrigin, ctx);
    return catalogDocument(request, env.ASSETS, read);
  }
  if (!apiPath(url.pathname)) {
    const accept = request.headers.get("Accept")?.trim().toLowerCase();
    const privatePage = isPrivatePage(url.pathname);
    const wantsPage = accept?.includes("text/html") || ((!accept || accept === "*/*") && (isPublicPage(url.pathname) || privatePage));
    const isPage = ["GET", "HEAD"].includes(request.method) && wantsPage
      && !/\.[^/]+$/.test(url.pathname) && !/^\/(static|img)(\/|$)/.test(url.pathname);
    return isPage ? pageDocument(request, env.ASSETS, privatePage) : env.ASSETS.fetch(request);
  }
  if (url.pathname === repairPath) return new Response(null, { status: 404 });
  if (url.pathname === probePath) {
    if (env.READINESS_ONLY !== "true" || !env.API || url.hostname !== "imladri-personal-candidate.imladri.workers.dev"
        || request.method !== "POST" || url.search || (request.body && request.headers.get("Content-Length") !== "0")) {
      return new Response(null, { status: 404 });
    }
    const headers = new Headers();
    const bearer = request.headers.get("Authorization");
    if (bearer) headers.set("Authorization", bearer);
    return env.API.fetch(new Request("https://container.internal" + probePath, { method: "POST", headers }));
  }
  if (env.READINESS_ONLY === "true") {
    const publicRead = ["GET", "HEAD"].includes(request.method) && (
      ["/api/", "/api/ready", "/api/public/config", "/api/public/catalog-paths", "/api/public/research/repeat", "/api/clerk/config", "/api/me"].includes(url.pathname)
      || /^\/api\/public\/(archetypes|figures)(\/|$)/.test(url.pathname) || /^\/api\/media\/[^/]+$/.test(url.pathname));
    const opsCheck = request.method === "POST" && url.pathname === "/api/ops/check";
    if (!publicRead && !opsCheck) return Response.json({ detail: "Not Found" }, { status: 404 });
  }
  const publicRead = canRetryPublicRead(request, url.pathname);
  const route = await backend(env, ctx, fetchOrigin, false, publicRead);
  if (!route.service && !route.origin) return Response.json({ detail: "API origin is unconfigured" }, { status: 503 });
  if (!await renderReady(route, env, fetchOrigin)) {
    // The primary may wake while standby spins up. Let the next request check again.
    if (env.API) readinessCache.delete(env.API);
    return originReconnecting();
  }
  const headers = new Headers(request.headers);
  headers.delete("Host");
  headers.delete("X-Imladri-Origin");
  headers.delete("X-Imladri-Client-IP");
  headers.delete("Forwarded");
  headers.delete("X-Forwarded-For");
  const clientIP = request.headers.get("CF-Connecting-IP");
  if (clientIP) {
    headers.set("X-Forwarded-For", clientIP);
    headers.set("X-Imladri-Client-IP", clientIP);
  }
  headers.set("X-Forwarded-Host", url.host);
  headers.set("X-Forwarded-Proto", url.protocol.slice(0, -1));
  async function forward({ service, origin, originSecret }, signal = request.signal) {
    const target = new URL(url);
    if (!service) {
      const upstreamURL = new URL(origin);
      target.protocol = upstreamURL.protocol;
      target.host = upstreamURL.host;
    }
    const upstreamHeaders = new Headers(headers);
    if (originSecret) upstreamHeaders.set("X-Imladri-Origin", originSecret);
    const forwarded = new Request(new Request(target, request), { headers: upstreamHeaders, redirect: "manual", signal });
    const upstream = service ? await service.fetch(forwarded) : await fetchOrigin(forwarded, { cache: "no-store" });
    return publicResponse(upstream, url, service ? "http://container" : target.origin);
  }

  // A GET can perform account maintenance. Limit retries to known public reads.
  if (!publicRead) return forward(route);
  async function readPublic(destination) {
    // Bound time to response headers. Let the response body stream at the reader’s pace.
    const controller = new AbortController();
    const signal = AbortSignal.any([request.signal, controller.signal]);
    let timer;
    try {
      const deadline = new Promise((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new Error("Public read deadline exceeded"));
        }, publicReadDeadlineMs);
      });
      return await Promise.race([forward(destination, signal), deadline]);
    } finally {
      clearTimeout(timer);
    }
  }
  let response;
  try {
    response = await readPublic(route);
  } catch (error) {
    if (request.signal.aborted) throw error;
  }
  if (!route.standby) return response || originReconnecting();
  if (response && (!transientGatewayStatuses.has(response.status) || request.signal.aborted)) return response;
  // Cancel the failed body without making standby wait for that origin's cleanup.
  discardBody(response);
  // Recheck health for the next private request. Only the public reads take this short bypass.
  readinessCache.delete(route.service);
  publicReadStandbyUntil.set(route.service, Date.now() + 5_000);
  try {
    if (!await renderReady(route.standby, env, fetchOrigin)) return originReconnecting();
    return await readPublic(route.standby);
  } catch (error) {
    if (request.signal.aborted) throw error;
    return Response.json({ detail: "The public catalog and configuration are temporarily unavailable." },
      { status: 503, headers: { "Cache-Control": "private, no-store", "Retry-After": "5" } });
  }
}

export async function checkOperations(env, fetchOrigin = fetch, ctx, scheduledTime) {
  let status = 503;
  const slot = new Date(scheduledTime);
  const maintainIngress = slot.getUTCHours() === 0 && slot.getUTCMinutes() < 15;
  const { service, origin, originSecret } = await backend(env, ctx, fetchOrigin, maintainIngress);
  if (env.OPS_CHECK_SECRET && (service || origin)) {
    const headers = new Headers({ Authorization: `Bearer ${env.OPS_CHECK_SECRET}` });
    if (originSecret) headers.set("X-Imladri-Origin", originSecret);
    const request = new Request(new URL("/api/ops/check", service ? "https://imladri.com" : origin), {
      method: "POST", headers, redirect: "manual"
    });
    try {
      const response = service ? await service.fetch(request) : await fetchOrigin(request, { cache: "no-store" });
      status = response.status;
      await response.body?.cancel();
    } catch { status = 502; }
  }
  if (status < 200 || status >= 300) throw new Error(`Operations check HTTP ${status}`);
}

export default {
  fetch: (request, env, ctx) => handle(request, env, fetch, ctx),
  scheduled: (event, env, ctx) => checkOperations(env, fetch, ctx, event.scheduledTime)
};
